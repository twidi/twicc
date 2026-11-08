import { baseCompile } from '@intlify/message-compiler'
import manifest from '../../../android/i18n/messages.json' with { type: 'json' }
import { i18n } from './index.js'

const formsBySource = new Map()
const decimal = /^-?(?:0|[1-9]\d*)(?:\.(\d+))?$/

// Format the integer separately so decimal strings never pass through Number.
function formatDecimal(value, locale) {
    if (typeof value === 'number') {
        if (!Number.isFinite(value)) return value
        const source = Object.is(value, -0) ? '-0' : String(value)
        if (!source.includes('e')) value = source
        else {
            const [coefficient, exponent] = source.split('e')
            const sign = coefficient.startsWith('-') ? '-' : ''
            const unsigned = coefficient.replace('-', '')
            const point = (unsigned.indexOf('.') < 0 ? unsigned.length : unsigned.indexOf('.')) + Number(exponent)
            const digits = unsigned.replace('.', '')
            value = sign + (point <= 0 ? `0.${'0'.repeat(-point)}${digits}`
                : point >= digits.length ? digits + '0'.repeat(point - digits.length)
                    : `${digits.slice(0, point)}.${digits.slice(point)}`)
        }
    }
    if (typeof value !== 'string' || !decimal.test(value)) return value
    const [integer, fraction] = value.split('.')
    const negativeZero = integer === '-0'
    const formatter = new Intl.NumberFormat(locale)
    const whole = formatter.format(negativeZero ? -0 : BigInt(integer))
    if (fraction === undefined) return whole
    const separator = formatter.formatToParts(1.1).find(part => part.type === 'decimal').value
    return `${whole}${separator}${fraction}`
}

export function formatMessageParams(key, params, locale) {
    const formats = manifest.messages[key]?.numberFormats ?? {}
    return Object.fromEntries(Object.entries(params).map(([name, value]) => [name,
        formats[name] === 'decimal' ? formatDecimal(value, locale) : value,
    ]))
}

function requiredForms(source) {
    if (!formsBySource.has(source)) {
        const { ast } = baseCompile(source, { jit: true, optimize: false, onError: error => { throw error } })
        const forms = ast.body.type === 1 ? ast.body.cases : [ast.body]
        formsBySource.set(source, forms.map(form => form.items.filter(item => item.type === 4).map(item => item.key)))
    }
    return formsBySource.get(source)
}

// The root counts as depth one. Traverse unused parameters as well as rendered ones.
export function toLocalMessageDescriptor(value, composer = i18n.global) {
    let nodes = 0
    function convert(message, depth) {
        if (depth > 4 || ++nodes > 64 || !message || typeof message !== 'object' || Array.isArray(message)) throw Error()
        if (typeof message.key !== 'string' || !composer.te(message.key, 'en')) throw Error()
        if (message.count !== undefined && (typeof message.count !== 'number' || !Number.isFinite(message.count))) throw Error()
        const params = Object.hasOwn(message, 'params') ? message.params : {}
        if (!params || typeof params !== 'object' || Array.isArray(params)) throw Error()
        const converted = Object.fromEntries(Object.entries(params).map(([name, value]) => {
            if (value !== null && typeof value === 'object') return [name, convert(value, depth + 1)]
            if (value !== null && !['string', 'number', 'boolean'].includes(typeof value)) throw Error()
            if (typeof value === 'number' && !Number.isFinite(value)) throw Error()
            return [name, value]
        }))
        const locale = composer.te(message.key, composer.locale.value) ? composer.locale.value : 'en'
        const source = composer.getLocaleMessage(locale)[message.key]
        if (typeof source !== 'string') throw Error()
        const forms = requiredForms(source)
        const choice = Math.abs(typeof params.count === 'number' ? params.count
            : typeof params.n === 'number' ? params.n : message.count ?? -1)
        const index = forms.length === 1 ? 0 : forms.length === 2
            ? (locale === 'fr' ? (choice < 2 ? 0 : 1) : (choice === 1 ? 0 : 1))
            : locale === 'fr' ? (choice === 0 ? 0 : choice === 1 ? 1 : 2) : Math.min(choice, 2)
        if (!forms[index]) throw Error()
        for (const name of forms[index]) {
            const parameter = converted[name] ?? (['count', 'n'].includes(name) ? message.count : undefined)
            if (parameter == null) throw Error()
            if (manifest.messages[message.key]?.numberFormats?.[name] === 'decimal'
                && !(typeof parameter === 'number' || (typeof parameter === 'string' && decimal.test(parameter)))) throw Error()
        }
        return { type: 'local-ui-message', key: message.key, params: converted,
            ...(message.count === undefined ? {} : { count: message.count }) }
    }
    try { return convert(value, 1) } catch { return null }
}
