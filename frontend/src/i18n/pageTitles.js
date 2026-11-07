import { i18n } from './index.js'

// Shells expose stable mode/kind data. Translate interface labels, not shared titles.
export function sharedPageTitle(data, composer = i18n.global) {
    if (data.mode === 'recent') return composer.t('share.pageTitle.recent')
    const artifact = data.mode === 'share' || data.mode === 'doc' || data.meta?.kind === 'artifact'
    const title = (data.meta?.title ?? data.title ?? '').trim()
    const key = artifact
        ? title ? 'share.pageTitle.artifactWithTitle' : 'share.pageTitle.artifact'
        : title ? 'share.pageTitle.sessionWithTitle' : 'share.pageTitle.session'
    return composer.t(key, { title })
}

export function artifactPageTitle(data, composer = i18n.global) {
    return data.mode === 'share' ? sharedPageTitle(data, composer) : composer.t('artifacts.pageTitle')
}
