/**
 * Shared asset-URL rewriting for rendered tip and help bodies.
 *
 * Authors reference local images/links inside a content folder (`tips/`
 * or `help/`) with any natural shape — `/tips/foo.png`, `./foo.png`, or
 * bare `foo.png` — and this rewrites them to the runtime BASE_URL (`/` in
 * dev, `/static/` in prod). It operates on parsed HTML (not the markdown
 * source) so occurrences inside code blocks are left alone. External URLs
 * (scheme, protocol-relative, anchors, other absolute paths) are untouched.
 * Covers both `<img src>` and `<a href>`.
 *
 * On top of that — in *any* content folder — a markdown link whose href is
 * exactly `help/<key>` (the canonical cross-link form) is turned into an
 * inert `<a data-help-key="<key>" href="#">`. The rendering component
 * delegates clicks to `utils/helpLinks.js`, which opens the help dialog.
 * This is what lets a tip link to a help page, and a help page link to
 * another.
 */
import { resolvePublicAssetUrl } from './publicAsset.js'

const HELP_LINK_RE = /^help\/([a-z0-9-]+)$/

// Resolve the complete path before adding the build-version query string.
// Locale-specific Markdown still shares the English folder's image assets.
export function resolveContentAssetUrl(value, folder) {
    if (!value || /^[a-z][a-z0-9+.-]*:/i.test(value) || value.startsWith('//') || value.startsWith('#')) return value
    const absPrefix = `/${folder}/`
    let path = value
    if (path.startsWith(absPrefix)) path = path.slice(absPrefix.length)
    else if (path.startsWith('/')) return value
    else if (path.startsWith('./')) path = path.slice(2)
    return resolvePublicAssetUrl(`${folder}/${path}`)
}

export function rewriteAssetUrls(html, { folder }) {
    const tmp = document.createElement('div')
    tmp.innerHTML = html

    const rewriteAttr = (el, attr) => {
        const v = el.getAttribute(attr)
        if (!v) return
        el.setAttribute(attr, resolveContentAssetUrl(v, folder))
    }

    tmp.querySelectorAll('img').forEach((el) => rewriteAttr(el, 'src'))
    tmp.querySelectorAll('a').forEach((el) => {
        const href = el.getAttribute('href')
        const m = href ? HELP_LINK_RE.exec(href) : null
        if (m) {
            // Cross-link to a help page: make it inert and tag it for the
            // delegated click handler (see utils/helpLinks.js).
            el.setAttribute('data-help-key', m[1])
            el.setAttribute('href', '#')
            return
        }
        rewriteAttr(el, 'href')
    })
    return tmp.innerHTML
}
