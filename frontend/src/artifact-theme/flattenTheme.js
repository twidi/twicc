// PostCSS plugin for the artifact theme bundle (vite.config.artifact-theme.js).
// The app switches schemes with classes on <html> (`.wa-dark`, `.wa-brand-cyan`, ...)
// that an artifact document does not carry, and Web Awesome declares its tokens in
// cascade layers. This flattens the app's theme sources into custom properties on
// `:root`: light selectors become `:root`, `.wa-dark` becomes
// `@media (prefers-color-scheme: dark) { :root }` (inside TwiCC the iframe's
// preferred scheme follows the app), everything else is dropped. Only custom
// properties survive, so the injected sheet never changes how a page renders.

// Selectors the app's <html> matches in the light scheme.
const LIGHT_SELECTORS = new Set([
    ':root', ':where(:root)', 'html',
    '.wa-theme-default', '.wa-light', '.wa-palette-default', '.wa-brand-cyan',
])
const DARK_SELECTORS = new Set(['.wa-dark'])

/** Hoist the content of every `@layer` block and drop layer order statements. */
function unwrapLayers(root) {
    root.walkAtRules('layer', atRule => {
        if (atRule.nodes) atRule.replaceWith(atRule.nodes)
        else atRule.remove()
    })
}

function rootRule(Rule, decls) {
    const rule = new Rule({ selector: ':root' })
    for (const decl of decls) rule.append(decl.clone())
    return rule
}

export function flattenTheme({ match = () => true } = {}) {
    return {
        postcssPlugin: 'twicc-flatten-artifact-theme',
        Once(root, { AtRule, Rule, result }) {
            if (!match(result.opts.from || '')) return
            unwrapLayers(root)
            for (const node of [...root.nodes]) {
                if (node.type !== 'rule') {
                    // Media queries, @property, @font-face, comments: not part of the token contract.
                    node.remove()
                    continue
                }
                const selectors = node.selectors.map(selector => selector.trim())
                const light = selectors.some(selector => LIGHT_SELECTORS.has(selector))
                const dark = selectors.some(selector => DARK_SELECTORS.has(selector))
                const decls = node.nodes.filter(child => child.type === 'decl' && child.prop.startsWith('--'))
                const replacements = []
                if (decls.length && light) replacements.push(rootRule(Rule, decls))
                if (decls.length && dark) {
                    const media = new AtRule({ name: 'media', params: '(prefers-color-scheme: dark)' })
                    media.append(rootRule(Rule, decls))
                    replacements.push(media)
                }
                if (replacements.length) node.replaceWith(replacements)
                else node.remove()
            }
        },
    }
}
flattenTheme.postcss = true
