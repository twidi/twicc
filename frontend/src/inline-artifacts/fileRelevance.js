/**
 * Ordinary pages keep root/top-folder relevance and exclude their own data store.
 * Inline pages accept only their selected artifact folder and exclude every data directory.
 * Tree refresh remains independent of preview reload.
 */
export function changeAffectsHtmlPage(renderedRel, paths) {
    const renderedSegments = renderedRel.split('/')
    const pageDir = renderedSegments.slice(0, -1).join('/')
    const dataPrefix = (pageDir ? pageDir + '/' : '') + 'data/'
    const relevant = paths.filter(p => p !== dataPrefix.slice(0, -1) && !p.startsWith(dataPrefix))
    if (renderedSegments[0] === 'inline-artifacts' && renderedSegments.length >= 3) {
        const prefix = renderedSegments.slice(0, 2).join('/') + '/'
        return relevant.some(p => p.startsWith(prefix) && !p.split('/').includes('data'))
    }
    if (renderedSegments.length <= 1) return relevant.length > 0
    return relevant.some(p => p.includes('/') && p.split('/')[0] === renderedSegments[0])
}
