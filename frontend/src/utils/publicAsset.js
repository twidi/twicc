/**
 * Resolve a path under `frontend/public/` to a runtime URL that works in
 * both dev and the built bundle.
 *
 * - In dev, Vite's `base` is `/`, so `import.meta.env.BASE_URL === '/'` and
 *   `frontend/public/foo.webp` is served at `/foo.webp`.
 * - In the built bundle, `base` is `/static/` (the path BlackNoise mounts the
 *   wheel-bundled assets at), so `import.meta.env.BASE_URL === '/static/'`.
 *
 * - In the built bundle the URL also carries `?v=<build timestamp>`: BlackNoise
 *   serves every `/static/` file as immutable for 10 years, and `public/` files
 *   keep their name across builds (no content hash), so without a changing URL
 *   a modified file would stay stale in browsers and CDNs. Dev has no version.
 *
 * Accepts both shapes — `frontend/public/<path>` and the already-stripped
 * `<path>` — so callers can pass whichever form they have at hand.
 *
 * @param {string} path - e.g. "frontend/public/whats-new/v1.5/foo.webp"
 *                        or "tips/welcome.md"
 * @returns {string} URL usable in `<img src>`, `fetch()`, `<a href>`, etc.
 */
export function resolvePublicAssetUrl(path) {
    const publicPath = path.replace(/^frontend\/public\//, '')
    const version = typeof __PUBLIC_ASSETS_VERSION__ === 'undefined' ? '' : __PUBLIC_ASSETS_VERSION__
    return (import.meta.env?.BASE_URL || '/') + publicPath + (version ? `?v=${version}` : '')
}
