import { getIconLibrary, registerIconLibrary } from '@awesome.me/webawesome/dist/components/icon/library.js'

/** Wrap the default icon library so `<wa-icon name="robot">` renders the SVG at `url`. */
export function registerBrandRobotIcon(url) {
    const defaultLibrary = getIconLibrary('default')
    if (!defaultLibrary) return
    registerIconLibrary('default', {
        ...defaultLibrary,
        resolver: (name, family, variant, autoWidth) => (
            name === 'robot' ? url : defaultLibrary.resolver(name, family, variant, autoWidth)
        ),
    })
}
